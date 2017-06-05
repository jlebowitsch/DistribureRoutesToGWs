
###########################################################################################################################################################
# to run the script in Lambda please create the following three environmental parameters
# 1. "elbname" with value '<MyELBName>'...this is the name of the ELB monitoring your gateway
# 2. "inputsubnets" with values in the format '['subnet1','subnet2'] ....this is the list of subnets behind the gateways
# 3. "Routetargets" with value in the format'['0.0.0.0/0', '192.168.1.0/24']....this is the list of route prefixes for destinations you want to be routed through the gateways.  


# do not change below
##########################################################################################################################################################



import boto3
import os



elbname=os.environ['elbname']
inputsubnets=os.environ['inputsubnets']
Routetargets=os.environ['Routetargets']


def lambda_handler(event, context):
    RouteSwitchv2(elbname,inputsubnets,Routetargets)
    return 'Hello from Lambda'
    

                 
            
def createGRSAZ(gwtable,inputsubnets,Routetargets):
    #find all the routing tables already associated with any healthy gws and their associated subnets
    ec2=boto3.client("ec2")
    rt2=ec2.describe_route_tables(Filters=[{'Name':'association.subnet-id','Values':[inputsubnets]}])
    M=[]
    for r in rt2['RouteTables']: 
        if set(Routetargets)<=set([rr['DestinationCidrBlock'] for rr in r['Routes'] if rr['InstanceId'] in [g[0] for g in gwtable if g[1]=='InService']]):
            for s in r['Associations']:
                z=min([n for n in range(len(r['Routes'])) if 'InstanceID' in r[n].keys() and r[n]['InstanceId'] in [g[0] for g in gwtable]])
                M.append(tuple([r[z]['InstanceId'],
                                    r['RouteTableId'],
                                    s['SubnetId'],
                                    1]))
    
        # add route tables that have the routes but no live GWs with index 2....we'll reuse these RTs and routes
        elif set(Routetargets)<=set([rr['DestinationCidrBlock'] for rr in r['Routes']]):
            for s in r['Associations']:
               z=min([n for n in range(len(r['Routes'])) if 'InstanceID' in r[n].keys() and r[n]['InstanceId'] in [g[0] for g in gwtable]])
               M.append(tuple([r[z]['InstanceId'],
                                r['RouteTableId'],
                                s['SubnetId'],
                                2]))  
  
    #add new RTs for any subnets that are not in the table. mark the GWs as NoGW and index at 3 so that we know that we need to add new routes
    subnets1=ec2.describe_subnets(Filters=[{'Name':"subnet-id",'Values':list(set(M[2]|set(inputsubnet)))}])
    subnets2={}
    for s in subnets1['Subnets']:
        subnets2.add({s['SubnetId']:s})
    elb=boto3.client('elb')
    vpcid=elb.describe_load_balancers(LoadBalancerNames=[elbname])['LoadBalancerDescriptions'][0]['VpcId']
    for sub in Inputsubnets:
        if not (sub in [m[2] for m in M]):
            if subnets2['sub']['VpcId']==vpcid:
                RTforS=ec2.create_route_table(VpcId=vpcid)['RouteTable']['RouteTableId']
                ec2.associate_route_table(SubnetId=sub, RouteTableId=RTforS)
                print ('created route table ',RTforS,' and associated it with subnet ',sub)
                M.append(tuple(['NoGW',RTforS,sub],3)) 
            else:
                print('Subnet ', sub, ' is in VPC ', subnet[sub][VpcId] ,' which is not in the same vpc as your gateways: (',vpcid, '). Ignoring!')
    
    # Convert to a list and add AZ info into table
    MM=[list(n) for n in set(M)]
    for r in MM:
        r.insert(3,subnets2[r[2]]['AvailabilityZone'])

    return MM
            
    
def RouteSwitchv2(elbname,inputsubnets,Routetables):
     gwtable=get_GWs_by_LB(elbname)
     if sum([1 for g in gwtable if g[1]=='InService'])>0: 
         grsaz=createGRSAZ(gwtable,inputsubnets,Routetables)
         for rt in set([r[1] for r in grsaz]):
             gwi=OptimalGWforRT(rt,gwtable,grsaz)
             if gwi != 'Current':
                 for g in set([g[0] for g in grsaz if g[1]==rt]):
                    ReplaceGWforRTinAWS(g,gwi,grsaz,rt,gwtable)
                    grsaz=ReplaceGWsforRTinGRSAZ(g,gwi,grsaz,rt)
             else:
                 print('route table ',rt, ' is already optimal in using the gateway: ', set([r[0] for r in grsaz if r[1]==rt]))
     
     else:
         print ("No GWs are up. Quiting")

         
def OptimalGWforRT(rt,gwtable,grsaz):
    
    response='Current'
    DRT=Dominant_AZ(grsaz)
    RTIsUp=[g[3] for g in grsaz if g[1]==rt].pop()==1
    GWsofRT=set([x[0] for x in grsaz if x[1]==rt]) # the set of gateways associated with the RT as someone could add there more than one by mistake
    RTinAZ = sum([1 for y in gwtable if y[0] in GWsofRT and (y[1]!='InService' or y[2]!=DRT[rt])])==0 # a boolean to determine if the RT currently already targets all inservice GW in the same AZ
    InAZGWs=[g for g in gwtable if g[1]=='InService' and g[2]==DRT[rt]] # the set of GWs Inseervice in the AZ
    RTSubnetCount={r:sum([1 for rr in grsaz if rr[1]==r]) for r in set([rr[1] for rr in grsaz])} 
    GWSubnetCount={g:sum([1 for rr in grsaz if rr[0]==g]) for g in [gg[0] for gg in gwtable]}
    AZRTSubnetCount=sum([RTSubnetCount[r] for r in DRT if DRT[r]==DRT[rt]])
    if RTIsUp and RTinAZ and sum([1 for x in InAZGWs if not x[0] in GWsofRT])>0:                                               #  if rt has live gws: if rt is in az and has others in az:
        if sum([1 for g in GWsofRT if (GWSubnetCount[g]-RTSubnetCount[rt]) > AZRTSubnetCount/len(InAZGWs)])>0:      # if there are "busier than than average GWs" associated with the route table
            print('route ', rt, 'is servered by GW in the right AZ but they are busier than everage.')
            M=[[GWSubnetCount[x[0]],x[0]] for x in InAZGWs if (GWSubnetCount[x[0]] + RTSubnetCount[rt]) < max([GWSubnetCount[x] for x in GWsofRT])]
            if len(M)>0:
                 response=min(M)[1]
            else:
                 print('no gw is sufficiently better to warrant a gw switch for route table ',rt)                            
            
    elif RTIsUp and not RTinAZ and sum([1 for x in InAZGWs if not x[0] in GWsofRT])==0:       
        if sum([1 for g in GWsofRT if (GWSubnetCount[g]-RTSubnetCount[rt]) > len(set([x[2] for x in grsaz]))/len([g for g in gwtable if g[1]=='InService'])])>0:
            print('route ', rt, 'has a busy GW in another AZ and there are no GWs in its AZ . looking for less busy GWs')     
            M=[[GWSubnetCount[x[0]],x[0]] for x in gwtable if x[1]=='InService' and GWSubnetCount[x[0]] + RTSubnetCount[rt] < max([GWSubnetCount[x] for x in GWsofRT])]
            if len(M)>0:
                 response=min(M)[1]
            else:
                 print('no gw is sufficiently better to warrant a gw switch for route ',rt) 
        else:
             print('Although served by a gw outside its AZ, no gw is sufficiently better to warrant a gw switch for route table ',rt) 
               
   
    elif ((RTisUp and not RTinAZ ) or not RTIsUP) and sum([1 for x in InAZGWs])>0:
         print('route ', rt, 'is not servered by GW in the right AZ and there are alternative GWs in AZ.')
         M=[[GWSubnetCount[x[0]],x[0]] for x in InAZGWs]
         response=min(M)[1]
       
   
    elif not RTIsUP:
       print('route ', rt, 'has GWs OutofService or is missing routes. There are GWs in Service outside its AZ')      
       M=[[GWSubnetCount[x[0]],x[0]] for x in gwtable if x[1]=='InService']
       response=min(M)[1]
           
    
    return response
    
    
   

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
    if [g[3] for g in grsaz if g[0]==gwo and g[1]==rtid].pop()<3 :
         for s in routetargets
             response=ec2.replace_route(DestinationCidrBlock=s,RouteTableId=rtid,NetworkInterfaceId=[gw[4][len(gw[4])-1][1] for gw in gwtable if gw[0]==gwi].pop() )
         print('in route table:', rtid, ' replacing gw: ', gwo,' with gw: ', gwi)
    else:
        for s in routetargets:
            response=ec2.create_route(DestinationCidrBlock=s, RouteTableId=rtid, NetworkInterfaceId=[gw[4][len(gw[4])-1][1] for gw in gwtable if gw[0]==gwi].pop())
        print('in route table:', rtid, ' added new routes with gw: ', gwi)
       
def RTsPointingtoDeadGWs(gwtable,grsaz):
    """provides a dict of route tables that have routes currently pointing to dead GWs, with the list of those GWs
    """
    
    S={}
    for r in list(set([g[1] for g in grsaz])): 
        if sum([1 for x in grsaz if x[1]==r and [y[1] for y in gwtable if y[0]==x[0]].pop()!='InService'])>0:
                M=[g[0] for g in grsaz if g[1]==r]
                S.update({r:M})          
    return S

            
                