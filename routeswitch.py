
import boto3


###########################################################################################################################################################
# Replace the name of the elb with the one that monitors your GW

elbname='MyELBName'

# there are 2 algorithms for deciding the distribution of GWs to Routes. 
# If you choose 1 here you'll get better distirbution of the load accross your gateways. 
# Specifically, if you have a number of subnets in some AZ, each with its own route table, then, if a new GW is added to that AZ then
# the route tables of some of these subnets will be changed to point to the new GW. 

UseFancyDecision=1 

# do not change below
##########################################################################################################################################################



def lambda_handler(event, context):
    if UseFancyDecision==1:
        RouteSwitchv2(elbname)
    else:    
        DealWithDownGW(elbname)
        DealWithUpGW(elbname)
    return 'Hello from Lambda'
    

def get_GWs_by_LB(x):
    """retrieves fron AWS all the GWs that are associated with an elb and arranges in a GW, GW health, AZ, VPC,eth0 and eth1 table
    """
    M=[] 
    elb=boto3.client('elb')
    ec2=boto3.client("ec2")
    ElbGW=elb.describe_instance_health(LoadBalancerName=x)
    GWs=ec2.describe_instances(Filters=[{"Name":"instance-id", "Values":[x['InstanceId'] for x in ElbGW['InstanceStates']]}])
    for re in GWs['Reservations']:
        for ins in re['Instances']:
            M.append([ins['InstanceId'], [y['State'] for y in ElbGW['InstanceStates'] if y['InstanceId']==ins['InstanceId']].pop(),ins['Placement']['AvailabilityZone'],ins['VpcId'],sorted([[eni['Attachment']['DeviceIndex'],eni['NetworkInterfaceId']] for eni in ins['NetworkInterfaces']])])            
    return M


def create_GRSAZ(gwtable):
    """takes a list of GWs and create a table with these gws, the route tables that refer to them, 
        the and the subnets associated with each such route table, with the subnet AZ
        """
    ec2=boto3.client("ec2")
    rt2=ec2.describe_route_tables(Filters=[{'Name':'route.instance-id','Values':[g[0] for g in gwtable]}])
#    print('rt2: ',rt2)
    M=[]
    for r in rt2['RouteTables']: 
        for s in r['Associations']:
            for ro in r['Routes']:
                if ('InstanceId' in ro.keys()):
                    M.append(tuple([ro['InstanceId'],r['RouteTableId'],s['SubnetId']]))

    MM=[list(n) for n in set(M)]
    Subnets1=ec2.describe_subnets(Filters=[{'Name':"subnet-id",'Values':[r[2] for r in MM]}]) 
    for r in MM:
        for s in Subnets1['Subnets']: 
            if (r[2]==s['SubnetId']) :
                r.append(s['AvailabilityZone'])

    return MM


def Dominant_AZ(grsaz):
    """Takes the GRSAZ list and returns another dict with the dominant AZ for reach route table
    """
    
    M={}
    RTs=list(set([y[1] for y in grsaz]))
    for rt in RTs:
        AZs=list(set([y[3] for y in grsaz if y[1]==rt]))
        MM=[]
        for az in AZs:
            MM.append([az, sum([1 for y in grsaz if (y[1]==rt and y[3]==az)])])
        mini=min([m[0] for m in MM if m[1]==max([m[1] for m in MM])]) #we take the first AZ that has the maximal number of subnets
        M.update({rt:mini})  
    return M

def BestGWforAZ(az,grsaz,gwtable):
    """Returns the least used GW that should be used for a route, given its dominant AZ
    """
    if len([1 for g in gwtable if g[1]=='InService' and g[2]==az])>0:
        S=[g for g in gwtable if g[1]=='InService' and g[2]==az]
    elif len([1 for g in gwtable if g[1]=='InService'])>0:   
        S=[g for g in gwtable if g[1]=='InService']
    else:
        raise ValueError('No GWs in Service')
    SS={}
    for s in S:
        SS.update({s[0]:sum([1 for g in grsaz if g[0]==s[0]])})            
    mini=min([s[0] for s in S if SS[s[0]]==min(SS.values())]) # we're taking the first GW that has the minimal number of subnets associated with it
   
    return mini

def ReplaceGWsforRTinGRSAZ(gwo,gwi,grsaz,rtid):
    """Changes one GW for another in the grsaz table for a given route
    """
    M=grsaz
    for r in M:
        if r[0]==gwo and r[1]==rtid:
            r[0]=gwi
    return M
       
def ReplaceGWforRTinAWS (gwo,gwi,grsaz,rtid,gwtable):
    """changes one GW for another in the all the routes of a given route table in AWS
    """
    ec2=boto3.client('ec2')
    rtd=ec2.describe_route_tables(RouteTableIds=[rtid])
    for r in rtd['RouteTables'][0]['Routes']:    
      if 'InstanceId' in r:      
        if r['InstanceId']==gwo:
            response=ec2.replace_route(DestinationCidrBlock=r['DestinationCidrBlock'],RouteTableId=rtid,NetworkInterfaceId=[gw[4][len(gw[4])-1][1] for gw in gwtable if gw[0]==gwi].pop() )
            print('in route:', rtid, ' replacing gw: ', gwo,' with gw: ', gwi)
#            print(r['InstanceId'],': ', response)
       
def RTsPointingtoDeadGWs(gwtable,grsaz):
    """provides a dict of route tables that have routes currently pointing to dead GWs, with the list of those GWs
    """
    
    S={}
    for r in list(set([g[1] for g in grsaz])): 
        if sum([1 for x in grsaz if x[1]==r and [y[1] for y in gwtable if y[0]==x[0]].pop()!='InService'])>0:
                M=[g[0] for g in grsaz if g[1]==r]
                S.update({r:M})          
    return S

    
def DealWithDownGW(elbname):
    """main function for dealing with routes with dead GWs, to be invoked on down health notification from an elb
    """
    gwtable=get_GWs_by_LB(elbname)
    if sum([1 for g in gwtable if g[1]=='InService'])>0:
        grsaz=create_GRSAZ(gwtable)
        RT=RTsPointingtoDeadGWs(gwtable,grsaz)
        if len(RT)>0:
            DRT=Dominant_AZ(grsaz)
            for r in RT:
 #               print('r: ',r)
                gwi=BestGWforAZ(DRT[r],grsaz,gwtable) #we really want one gw per route table
                for g in RT[r]:
                    ReplaceGWforRTinAWS(g,gwi,grsaz,r,gwtable)
                    grsaz=ReplaceGWsforRTinGRSAZ(g,gwi,grsaz,r)
            if sorted(grsaz)==sorted(create_GRSAZ(gwtable)):
                print('finished down gatewaying. Amen')
            else:
                print('more orphan routes need to be dealth with. please run again')
        else:
            print('No orphan routes')
    else:
        print("No GWs are up. Quiting")
            
 
def DealWithUpGW(elbname):
    """main function for dealing with new GWs. it reoptimizes route distribution across GWs, to be invoked on up health notification from elb
    """
    gwtable=get_GWs_by_LB(elbname)
    grsaz=create_GRSAZ(gwtable)
    UnusedGWs=GetUnusedGWs(gwtable,grsaz)
#    print('grsaz: ', grsaz)
#    print('gwtable: ', gwtable)
#    print(UnusedGWs)
    if len(UnusedGWs)>0:
        for g in UnusedGWs:
            UseNewGW(g,grsaz,gwtable)
        print('finished up gatewaying. Amen')
        
       
    else:
        print("No GWs are idle. Quiting")
    

def GetUnusedGWs(gwtable,grsaz):
    
    M=[g for g in gwtable if sum([1 for gw in grsaz if g[0]==gw[0]])==0 and g[1]=='InService']
    return M

def UseNewGW(g,grsaz,gwtable):
    DRT=Dominant_AZ(grsaz)
 #   print('DRT: ', DRT)
 #   print('grsaz: ',grsaz)
 #   print('gwtable: ', gwtable)
 #   print('g: ',g)
    for r in DRT: 
 #       print('r: ',r,'  logic: ',  (DRT[r]==g[2] and sum([1 for x in grsaz if x[1]==r and [gg[2] for gg in gwtable if gg[0]==x[0]].pop()!=g[2]])>0) or sum([1 for gw in grsaz if gw[1]==r and [gg[1] for gg in gwtable if gg[0]==gw[0]].pop()!='InService'])>0 )
        if (DRT[r]==g[2] and sum([1 for x in grsaz if x[1]==r and [gg[2] for gg in gwtable if gg[0]==x[0]].pop()!=g[2]])>0) or sum([1 for gw in grsaz if gw[1]==r and [gg[1] for gg in gwtable if gg[0]==gw[0]].pop()!='InService'])>0:
            for gwo in set([gw[0] for gw in grsaz if gw[1]==r]):
                ReplaceGWforRTinAWS (gwo,g[0],grsaz,r,gwtable)
                grsaz=ReplaceGWsforRTinGRSAZ(gwo,g[0],grsaz,r)
                
                
def RouteSwitchv2(elbname):
     gwtable=get_GWs_by_LB(elbname)
     if sum([1 for g in gwtable if g[1]=='InService'])>0: 
         grsaz=create_GRSAZ(gwtable)
         if len(grsaz)>0:
             for rt in set([r[1] for r in grsaz]):
                 gwi=OptimalGWforRT(rt,gwtable,grsaz)
                 if gwi != 'Current':
                     for g in set([g[0] for g in grsaz if g[1]==rt]):
                        ReplaceGWforRTinAWS(g,gwi,grsaz,rt,gwtable)
                        grsaz=ReplaceGWsforRTinGRSAZ(g,gwi,grsaz,rt)
                 else:
                     print('route table ',rt, ' is already optimal in using the gateway: ', set([r[0] for r in grsaz if r[1]==rt]))
         else:
             print('No Routes are associated with any of the GWs monitored by the ELB' )
     else:
         print ("No GWs are up. Quiting")

         
def OptimalGWforRT(rt,gwtable,grsaz):
    
    response='Current'
    DRT=Dominant_AZ(grsaz)
    GWsofRT=set([x[0] for x in grsaz if x[1]==rt]) # the set of gateways associated with the RT as someone could add there more than one by mistake
    RTinAZ = sum([1 for y in gwtable if y[0] in GWsofRT and (y[1]!='InService' or y[2]!=DRT[rt])])==0 # a boolean to determine if the RT currently already targets all inservice GW in the same AZ
    InAZGWs=[g for g in gwtable if g[1]=='InService' and g[2]==DRT[rt]] # the set of GWs Inseervice in the AZ
    RTSubnetCount={r:sum([1 for rr in grsaz if rr[1]==r]) for r in set([rr[1] for rr in grsaz])} 
    GWSubnetCount={g:sum([1 for rr in grsaz if rr[0]==g]) for g in [gg[0] for gg in gwtable]}
    AZRTSubnetCount=sum([RTSubnetCount[r] for r in DRT if DRT[r]==DRT[rt]])
    if RTinAZ and sum([1 for x in InAZGWs if not x[0] in GWsofRT])>0: # that is, if the route table is in the AZ and there are some GWs in the AZ that are not already targeted by the route table
        if sum([1 for g in GWsofRT if (GWSubnetCount[g]-RTSubnetCount[rt]) > AZRTSubnetCount/len(InAZGWs)])>0: # if there are "busier than than average GWs" associated with the route table
            print('route ', rt, 'is servered by GW in the right AZ but there are alternative GWs.')
            M=[[GWSubnetCount[x[0]],x[0]] for x in InAZGWs if (GWSubnetCount[x[0]] + RTSubnetCount[rt]) < max([GWSubnetCount[x] for x in GWsofRT])]
            if len(M)>0:
                response=min(M)[1]
            
            else:
                print('No GW is better for ', rt)
    elif not RTinAZ:
        if len(InAZGWs)>0:
            print('route ', rt, 'is not servered by GW in the right AZ and there are alternative GWs in AZ.')
            M=[[GWSubnetCount[x[0]],x[0]] for x in InAZGWs]
            response=min(M)[1]
       
        elif sum([1 for g in GWsofRT if [x[1] for x in gwtable if g==x[0]].pop()!='InService'])>0:
            print('route ', rt, 'has GWs OutofService. There are GWs in Service outside its AZ')      
            M=[[GWSubnetCount[x[0]],x[0]] for x in gwtable if x[1]=='InService']
            response=min(M)[1]
                  
        elif sum([1 for g in GWsofRT if (GWSubnetCount[g]-RTSubnetCount[rt]) > len(set([x[2] for x in grsaz]))/len([g for g in gwtable if g[1]=='InService'])])>0:
            print('route ', rt, 'has a busy GW in another AZ and there are no GWs in its AZ . looking for less busy GWs')     
            M=[[GWSubnetCount[x[0]],x[0]] for x in gwtable if x[1]=='InService' and GWSubnetCount[x[0]] + RTSubnetCount[rt] < max([GWSubnetCount[x] for x in GWsofRT])]
            response=min(M)[1]
    return response
        
        